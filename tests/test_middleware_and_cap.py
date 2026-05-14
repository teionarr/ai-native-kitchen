"""Tests for the cost-telemetry middleware + the daily-cap dependency.

Both depend on the cost module — we patch cost.record / cost.daily_total directly
rather than spinning up Postgres."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

import src.cost as cost_mod
from src.main import app


@pytest.fixture
def env_token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("SERVICE_BEARER_TOKEN_RESEARCH_COMPANY", "tok")
    return "tok"


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---- Cost telemetry middleware -----------------------------------------------------------------


async def test_middleware_records_authenticated_request(
    client: AsyncClient, env_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = AsyncMock()
    monkeypatch.setattr(cost_mod, "record", record)
    monkeypatch.setattr(cost_mod, "daily_total", AsyncMock(return_value=0.0))

    # /people 503s (no provider) but the auth dep ran first → skill_id is set → telemetry records
    resp = await client.post("/people", json={"company": "X"}, headers={"Authorization": f"Bearer {env_token}"})
    assert resp.status_code == 503
    record.assert_called_once()
    kwargs = record.call_args.kwargs
    assert kwargs["skill_id"] == "research-company"
    assert kwargs["endpoint"] == "/people"
    assert kwargs["status_code"] == 503
    assert kwargs["duration_ms"] >= 0
    assert kwargs["cache_hit"] is False


async def test_middleware_skips_unauthenticated_request(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    record = AsyncMock()
    monkeypatch.setattr(cost_mod, "record", record)

    # /health is unauthenticated — middleware should NOT record (no skill_id on state)
    await client.get("/health")
    record.assert_not_called()


async def test_middleware_skips_401_responses(
    client: AsyncClient, env_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 401 means auth failed BEFORE setting skill_id; nothing to record."""
    record = AsyncMock()
    monkeypatch.setattr(cost_mod, "record", record)

    resp = await client.post(
        "/people",
        json={"company": "X"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401
    record.assert_not_called()


# ---- Daily cap enforcement ---------------------------------------------------------------------


async def test_request_under_cap_is_allowed(
    client: AsyncClient, env_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cost_mod, "daily_total", AsyncMock(return_value=5.0))
    monkeypatch.setattr(cost_mod, "record", AsyncMock())
    # Default cap is 20.0; 5.0 spent → still well under
    resp = await client.post("/people", json={"company": "X"}, headers={"Authorization": f"Bearer {env_token}"})
    # 503 because /people has no provider, but it got past auth — that's the contract
    assert resp.status_code == 503


async def test_request_over_cap_returns_429(
    client: AsyncClient, env_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cost_mod, "daily_total", AsyncMock(return_value=20.50))
    monkeypatch.setattr(cost_mod, "record", AsyncMock())
    resp = await client.post("/people", json={"company": "X"}, headers={"Authorization": f"Bearer {env_token}"})
    assert resp.status_code == 429
    detail = resp.json()["detail"]
    assert detail["error"] == "daily_cap_exceeded"
    assert detail["skill_id"] == "research-company"
    assert detail["spent_usd"] == 20.50
    assert detail["cap_usd"] == 20.0


async def test_cap_zero_disables_check(client: AsyncClient, env_token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """max_daily_usd_per_skill=0 → cap disabled even if spent > 0."""
    from src.config import settings

    monkeypatch.setattr(settings, "max_daily_usd_per_skill", 0.0)
    daily_total = AsyncMock(return_value=999.0)
    monkeypatch.setattr(cost_mod, "daily_total", daily_total)
    monkeypatch.setattr(cost_mod, "record", AsyncMock())

    resp = await client.post("/people", json={"company": "X"}, headers={"Authorization": f"Bearer {env_token}"})
    assert resp.status_code == 503  # past auth, into route's 503
    # daily_total isn't even called when the cap is 0
    daily_total.assert_not_called()


async def test_cap_exceeded_records_429_for_analytics(
    client: AsyncClient, env_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap check raises HTTPException AFTER setting request.state.skill_id.
    Starlette preserves request.state across the exception boundary, so the cost
    middleware sees skill_id and records the 429. This gives operators visibility
    into "which skill is hitting the cap, how often" via cost_log analytics."""
    monkeypatch.setattr(cost_mod, "daily_total", AsyncMock(return_value=999.0))
    record = AsyncMock()
    monkeypatch.setattr(cost_mod, "record", record)

    resp = await client.post("/people", json={"company": "X"}, headers={"Authorization": f"Bearer {env_token}"})
    assert resp.status_code == 429
    record.assert_called_once()
    assert record.call_args.kwargs["skill_id"] == "research-company"
    assert record.call_args.kwargs["status_code"] == 429
