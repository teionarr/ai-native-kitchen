"""Tests for the /health endpoint and the request-id middleware.

Uses httpx ASGITransport — no real HTTP socket, just in-process. Fast and
hermetic.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health_returns_200(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200


async def test_health_payload_shape(client: AsyncClient) -> None:
    resp = await client.get("/health")
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert isinstance(body["version"], str)
    assert isinstance(body["uptime_s"], int)
    assert body["uptime_s"] >= 0


async def test_request_id_header_added_when_absent(client: AsyncClient) -> None:
    resp = await client.get("/health")
    rid = resp.headers.get("x-request-id")
    assert rid is not None
    assert len(rid) >= 32  # uuid4 hex is 36 chars; allow a bit of slack


async def test_request_id_preserved_when_provided(client: AsyncClient) -> None:
    resp = await client.get("/health", headers={"X-Request-Id": "test-correlation-1234"})
    assert resp.headers.get("x-request-id") == "test-correlation-1234"


async def test_docs_disabled_by_default(client: AsyncClient) -> None:
    """KITCHEN_ENABLE_DOCS defaults to false — /docs and /openapi.json should 404."""
    resp = await client.get("/docs")
    assert resp.status_code == 404
    resp = await client.get("/openapi.json")
    assert resp.status_code == 404
