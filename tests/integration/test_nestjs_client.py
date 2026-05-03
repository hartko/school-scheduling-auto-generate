"""
Integration tests for the NestJS HTTP client.

Uses respx to intercept httpx requests at the transport level —
no real NestJS server needed.
"""

import pytest
import respx
from httpx import Response

from app.clients.nestjs_client import NestJSClient
from app.core.exceptions import NestJSClientError


def _paginated(data: list, page: int, total_pages: int) -> dict:
    """Build a fake NestJS paginated response."""
    return {
        "data": data,
        "pagination": {
            "totalItems": len(data) * total_pages,
            "currentPage": page,
            "itemsPerPage": len(data),
            "itemCount": len(data),
            "totalPages": total_pages,
        },
    }


@pytest.mark.asyncio
@respx.mock
async def test_fetches_all_pages():
    """_fetch_all_pages must collect items from every page."""
    # Mock page 1 → 2 items, totalPages=2
    respx.get("http://localhost:4000/teachers", params={"page": 1, "limit": 100}).mock(
        return_value=Response(
            200,
            json=_paginated(
                [
                    {"id": 1, "first_name": "Alice", "last_name": "Smith"},
                    {"id": 2, "first_name": "Bob",   "last_name": "Jones"},
                ],
                page=1,
                total_pages=2,
            ),
        )
    )
    # Mock page 2 → 1 item, totalPages=2
    respx.get("http://localhost:4000/teachers", params={"page": 2, "limit": 100}).mock(
        return_value=Response(
            200,
            json=_paginated(
                [{"id": 3, "first_name": "Carol", "last_name": "White"}],
                page=2,
                total_pages=2,
            ),
        )
    )

    async with NestJSClient() as client:
        teachers = await client.get_teachers()

    assert len(teachers) == 3
    assert teachers[0].first_name == "Alice"
    assert teachers[2].first_name == "Carol"


@pytest.mark.asyncio
@respx.mock
async def test_raises_after_max_retries():
    """NestJSClientError must be raised when all retries are exhausted."""
    # Make every request fail with a connection error
    respx.get("http://localhost:4000/rooms").mock(side_effect=Exception("connection refused"))

    with pytest.raises(NestJSClientError):
        async with NestJSClient() as client:
            await client.get_rooms()
