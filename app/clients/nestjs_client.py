"""
Async HTTP client for the NestJS school-scheduling backend.

Key responsibilities:
  - Fetch every page of every paginated endpoint and return a flat list
  - Retry transient network errors with exponential back-off
  - Use the correct endpoint paths (some have /api/ prefix or singular names)

Confirmed endpoint paths from the NestJS codebase:
  - /teachers, /rooms, /subjects, /sections, /schedules, /teacher-subjects
  - /api/room-schedules   ← note the /api/ prefix (RoomSchedulesController)
  - /class-group          ← singular, not /class-groups
"""

from __future__ import annotations

import asyncio
from typing import TypeVar

import httpx
import structlog

from app.core.config import settings
from app.core.exceptions import NestJSClientError
from app.schemas.nestjs import (
    ClassGroup,
    PaginatedResponse,
    Room,
    RoomSchedule,
    Schedule,
    Section,
    Subject,
    Teacher,
    TeacherSubject,
)

log = structlog.get_logger()
T = TypeVar("T")


class NestJSClient:
    """
    Async context manager that wraps an httpx.AsyncClient.

    Usage:
        async with NestJSClient() as client:
            teachers = await client.get_teachers()
    """

    def __init__(self) -> None:
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "NestJSClient":
        self._http = httpx.AsyncClient(
            base_url=str(settings.nestjs_base_url),
            timeout=settings.nestjs_timeout_seconds,
            # Tell NestJS we speak JSON
            headers={"Content-Type": "application/json"},
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._http:
            await self._http.aclose()

    # ── Pagination helper ─────────────────────────────────────────────────────

    async def _fetch_all_pages(self, path: str, model: type[T]) -> list[T]:
        """
        Fetch every page from a paginated NestJS endpoint and return a flat list.

        Strategy:
          1. Fetch page 1 to discover totalPages.
          2. Fetch pages 2..totalPages sequentially.
          3. Retry up to `nestjs_max_retries` times on transient transport errors,
             using exponential back-off (1s, 2s, 4s …).

        Args:
            path:  URL path relative to NESTJS_BASE_URL (e.g. "/teachers")
            model: Pydantic model class used to validate each item in `data[]`
        """
        assert self._http is not None, "Client must be used as a context manager"

        results: list[T] = []
        page = 1
        limit = settings.nestjs_page_size

        while True:
            # --- retry loop ---
            last_exc: Exception | None = None
            for attempt in range(settings.nestjs_max_retries):
                try:
                    resp = await self._http.get(
                        path, params={"page": page, "limit": limit}
                    )
                    resp.raise_for_status()
                    last_exc = None
                    break
                except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                    last_exc = exc
                    wait = 2 ** attempt  # 1s, 2s, 4s …
                    log.warning(
                        "nestjs_client.retry",
                        path=path,
                        attempt=attempt + 1,
                        max=settings.nestjs_max_retries,
                        wait_seconds=wait,
                    )
                    await asyncio.sleep(wait)

            if last_exc is not None:
                raise NestJSClientError(
                    f"Failed to fetch {path} after {settings.nestjs_max_retries} attempts: {last_exc}"
                )

            # Validate response using a generic PaginatedResponse[model]
            body = resp.json()
            parsed: PaginatedResponse[model] = PaginatedResponse[model].model_validate(body)  # type: ignore[valid-type]
            results.extend(parsed.data)

            log.debug(
                "nestjs_client.page_fetched",
                path=path,
                page=page,
                total_pages=parsed.pagination.totalPages,
                items=len(parsed.data),
            )

            # Stop when we have fetched the last page
            if page >= parsed.pagination.totalPages:
                break
            page += 1

        log.info("nestjs_client.fetched_all", path=path, total=len(results))
        return results

    # ── Entity fetchers ───────────────────────────────────────────────────────

    async def get_teachers(self) -> list[Teacher]:
        return await self._fetch_all_pages("/teachers", Teacher)

    async def get_rooms(self) -> list[Room]:
        return await self._fetch_all_pages("/rooms", Room)

    async def get_subjects(self) -> list[Subject]:
        return await self._fetch_all_pages("/subjects", Subject)

    async def get_sections(self) -> list[Section]:
        return await self._fetch_all_pages("/sections", Section)

    async def get_schedules(self) -> list[Schedule]:
        # The paginated list endpoint does NOT include scheduleTimes[].
        # Fetch the full detail for each schedule to get its time slots.
        assert self._http is not None
        stubs = await self._fetch_all_pages("/schedules", Schedule)
        detailed: list[Schedule] = []
        for s in stubs:
            resp = await self._http.get(f"/schedules/{s.id}")
            resp.raise_for_status()
            detailed.append(Schedule.model_validate(resp.json()))
        log.info("nestjs_client.schedules_enriched", total=len(detailed))
        return detailed

    async def get_teacher_subjects(self) -> list[TeacherSubject]:
        return await self._fetch_all_pages("/teacher-subjects", TeacherSubject)

    async def get_room_schedules(self) -> list[RoomSchedule]:
        # Uses /api/ prefix — confirmed from NestJS RoomSchedulesController
        return await self._fetch_all_pages("/api/room-schedules", RoomSchedule)

    async def get_class_groups(self) -> list[ClassGroup]:
        # Singular /class-group — confirmed from NestJS ClassGroupController
        return await self._fetch_all_pages("/class-group", ClassGroup)

    # ── Write operations ──────────────────────────────────────────────────────

    async def create_class_group(
        self,
        teacher_subject_id: int,
        room_schedule_id: int,
        section_id: int,
        schedule_time_id: int | None = None,
    ) -> ClassGroup:
        """POST a single class group to NestJS and return the created record."""
        assert self._http is not None

        payload: dict = {
            "teacher_subject_id": teacher_subject_id,
            "room_schedule_id": room_schedule_id,
            "section_id": section_id,
        }
        if schedule_time_id is not None:
            payload["schedule_time_id"] = schedule_time_id

        resp = await self._http.post("/class-group", json=payload)
        resp.raise_for_status()
        return ClassGroup.model_validate(resp.json())

    async def bulk_create_class_groups(self, items: list[dict]) -> dict:
        """POST all class groups in one request via POST /class-group/bulk."""
        assert self._http is not None
        resp = await self._http.post("/class-group/bulk", json={"items": items})
        resp.raise_for_status()
        return resp.json()
